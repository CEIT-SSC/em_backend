export interface CertificateRequest {
    /**
    * @maxLength 255
    */
    name: string;
}

export interface Certificate {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @maxLength 255
    */
    nameOnCertificate: string;
    file?: File | null;
    isVerified?: boolean;
    /**
    * @format date-time
    */
    requestedAt?: string;
}

export interface CompletedEnrollment {
    /**
    * @label ID
    */
    id?: number;
    presentationTitle?: string;
    hasCertificate?: boolean;
    isCertificateVerified?: boolean;
}

export interface ErrorResponse {
    error: string;
}

export interface MessageResponse {
    message: string;
}

